// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title OrcaMail
 * @notice Wallet-to-wallet encrypted messaging service on Lightchain
 * @dev Part of the OrcaMail project (orcamail.ai)
 */
contract OrcaMail {
    // ─────────────────────────────────────────────
    //  State
    // ─────────────────────────────────────────────
    address public owner;

    uint256 public sendFee;   // fee per message in LCAI (wei)
    uint256 public bulkFee;   // fee per recipient for bulk sends (wei)

    // Preference bitmask flags:
    //   bit 0 (1) = personal messages
    //   bit 1 (2) = announcements
    //   bit 2 (4) = ads
    struct MailPrefs {
        bool    optedIn;
        uint8   preferences; // bitmask
    }

    mapping(address => MailPrefs) private _prefs;

    // ─────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────
    event MailSent(
        address indexed from,
        address indexed to,
        bytes32         contentHash,
        string          storageRef,
        uint8           messageType,
        uint256         timestamp
    );

    event OptedIn(address indexed wallet, uint8 preferences);
    event OptedOut(address indexed wallet);
    event SendFeeUpdated(uint256 newFee);
    event BulkFeeUpdated(uint256 newFee);
    event FeesWithdrawn(address indexed to, uint256 amount);

    // ─────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == owner, "OrcaMail: not owner");
        _;
    }

    // ─────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────
    constructor(uint256 _sendFee, uint256 _bulkFee) {
        owner   = msg.sender;
        sendFee = _sendFee;
        bulkFee = _bulkFee;
    }

    // ─────────────────────────────────────────────
    //  Opt-in / opt-out
    // ─────────────────────────────────────────────

    /**
     * @notice Register to receive OrcaMail messages.
     * @param preferences Bitmask: 1=personal, 2=announcements, 4=ads
     */
    function optIn(uint8 preferences) external {
        _prefs[msg.sender] = MailPrefs({ optedIn: true, preferences: preferences });
        emit OptedIn(msg.sender, preferences);
    }

    /**
     * @notice Update message-type preferences without opting out.
     * @param preferences New bitmask value.
     */
    function updatePreferences(uint8 preferences) external {
        require(_prefs[msg.sender].optedIn, "OrcaMail: not opted in");
        _prefs[msg.sender].preferences = preferences;
        emit OptedIn(msg.sender, preferences);
    }

    /**
     * @notice Remove yourself from the registry.
     */
    function optOut() external {
        delete _prefs[msg.sender];
        emit OptedOut(msg.sender);
    }

    // ─────────────────────────────────────────────
    //  Messaging
    // ─────────────────────────────────────────────

    /**
     * @notice Send an encrypted message to a single opted-in wallet.
     * @param to          Recipient address (must have opted in).
     * @param contentHash keccak256 hash of the encrypted content.
     * @param storageRef  URL or backend ID where the ciphertext is stored.
     * @param messageType Semantic type (e.g. 0=personal, 1=announcement, 2=ad).
     */
    function sendMail(
        address to,
        bytes32 contentHash,
        string calldata storageRef,
        uint8 messageType
    ) external payable {
        require(msg.value >= sendFee, "OrcaMail: insufficient fee");
        require(_prefs[to].optedIn,   "OrcaMail: recipient not opted in");

        emit MailSent(
            msg.sender,
            to,
            contentHash,
            storageRef,
            messageType,
            block.timestamp
        );

        // Refund any overpayment
        if (msg.value > sendFee) {
            payable(msg.sender).transfer(msg.value - sendFee);
        }
    }

    /**
     * @notice Bulk-send a message to multiple recipients (owner only).
     * @dev    Caller pays bulkFee × recipients.length in LCAI.
     * @param recipients  Array of opted-in addresses.
     * @param contentHash keccak256 hash of the shared encrypted content.
     * @param storageRef  URL or backend ID where the ciphertext is stored.
     * @param messageType Semantic type byte.
     */
    function bulkSendMail(
        address[] calldata recipients,
        bytes32 contentHash,
        string calldata storageRef,
        uint8 messageType
    ) external payable onlyOwner {
        uint256 total = bulkFee * recipients.length;
        require(msg.value >= total, "OrcaMail: insufficient bulk fee");

        uint256 ts = block.timestamp;
        for (uint256 i = 0; i < recipients.length; i++) {
            if (_prefs[recipients[i]].optedIn) {
                emit MailSent(
                    msg.sender,
                    recipients[i],
                    contentHash,
                    storageRef,
                    messageType,
                    ts
                );
            }
        }

        // Refund any overpayment
        if (msg.value > total) {
            payable(msg.sender).transfer(msg.value - total);
        }
    }

    // ─────────────────────────────────────────────
    //  Owner controls
    // ─────────────────────────────────────────────

    function setSendFee(uint256 newFee) external onlyOwner {
        sendFee = newFee;
        emit SendFeeUpdated(newFee);
    }

    function setBulkFee(uint256 newFee) external onlyOwner {
        bulkFee = newFee;
        emit BulkFeeUpdated(newFee);
    }

    function withdrawFees() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "OrcaMail: nothing to withdraw");
        payable(owner).transfer(balance);
        emit FeesWithdrawn(owner, balance);
    }

    // ─────────────────────────────────────────────
    //  View helpers
    // ─────────────────────────────────────────────

    function hasOptedIn(address wallet) external view returns (bool) {
        return _prefs[wallet].optedIn;
    }

    function getPreferences(address wallet) external view returns (uint8) {
        return _prefs[wallet].preferences;
    }

    function getSendFee() external view returns (uint256) {
        return sendFee;
    }

    function getBulkFee() external view returns (uint256) {
        return bulkFee;
    }

    // Allow contract to receive LCAI directly (e.g., top-ups)
    receive() external payable {}
}
